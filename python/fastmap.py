import h5py
import itertools
import marshal
import multiprocessing as mp
import numpy as np
import pandas as pd
import pathlib
import pickle
import scipy.signal
import sklearn.model_selection
import sklearn.svm
import tqdm.notebook as tqdm
import types

def _init_pdist(fastmap, _X1, _X2, _W1, _W2):

    global self, X1, X2, W1, W2
    
    self = fastmap
    X1 = _X1
    X2 = _X2
    W1 = _W1
    W2 = _W2

    
def _pdist(iobj, jobj):
    
    global self, X1, X2, W1, W2

    dist = self._distance(X1[iobj], X2[jobj])

    for i in range(self._ihyprpln):
        if dist**2 < (W1[iobj, i] - W2[jobj, i])**2:
            return (0)
        dist = np.sqrt(dist**2 - (W1[iobj, i] - W2[jobj, i])**2)

    return (dist)


def correlate(a, b, mode="valid"):

    if len(a) > len(b):
        a, b = b, a

    a = pd.Series(a)
    b = pd.Series(b)
    n = len(a)

    a = a - np.mean(a)
    b = b - np.mean(b)
    
    c = scipy.signal.correlate(b, a, mode=mode)
    
    # if mode == "valid":
    #     norm = n * np.std(a) * b.rolling(n).std().dropna().values
    # elif mode == "same":
    #     norm = n * np.std(a) * b.rolling(n, min_periods=0, center=True).std().values
        
    norm = n * np.std(a) * np.std(b)
    if norm == 0:
        c[:] = 0
    else:
        c /= norm
    
    return (c)


def distance(
    obj_a, 
    obj_b, 
    mode="valid", 
    # reduce=_reduce, 
    force_triangle_ineq=False
):
    """
    Return the distance between object obj_a and object obj_b.
    
    Arguments:
    - obj_a: object
        First object to consider.
    - obj_b: object
        Second object to consider.
    """
    dist = 1 - np.max(np.abs(ndcorrelate(obj_a, obj_b, mode=mode, reduce=reduce)))
    
    if force_triangle_ineq is True:
        if dist == 0:
            return (0)
        else:
            return ((dist + 1) / 2)

    else:
        return (dist)


def ndcorrelate(a, b, mode="valid"):

    assert a.ndim == b.ndim, "a and b must have the same number of dimensions"
    
    if a.ndim == 1:
        return (correlate(a, b, mode=mode))

    assert a.shape[:-1] == b.shape[:-1]
    
    na, nb = a.shape[-1], b.shape[-1]
    
    if na > nb:
        a, b = b, a
        na, nb = nb, na

    a = a.reshape(-1, na)
    b = b.reshape(-1, nb)
    n = a.shape[0]
    
    if mode == "valid":
        c = np.zeros((n, nb - na + 1))
    elif mode == "same":
        c = np.zeros((n, nb))
    for i in range(n):
        c[i] = correlate(a[i], b[i], mode=mode)
    
    return (c)


class FastMapSVM(object):


    def __init__(self, distance, ndim, model_path):
        self._distance = distance
        self._ihyprpln = 0
        self._ndim = ndim
        self._init_hdf5(pathlib.Path(model_path))


    @property
    def hdf5(self):
        """
        HDF5 model backend.
        """
        
        return (self._hdf5)
    
    @property
    def ndim(self):
        """
        Dimensionality of embedding.
        """
        return (self._ndim)

    
    @property
    def X_piv(self):
        """
        Pivot objects.
        """
        
        if "X_piv" not in self.hdf5:
            self.hdf5.create_dataset(
                "X_piv", 
                (self.ndim, 2, *self.X.shape[1:]), 
                self.X.dtype,
                fillvalue=np.nan
            )
            
        return (self.hdf5["X_piv"])
    
    @property
    def pivot_ids(self):
        """
        Indices of pivot objects.
        """
        
        if "pivot_ids" not in self.hdf5:
            self.hdf5.create_dataset(
                "pivot_ids", 
                (self.ndim, 2), 
                np.uint16,
                fillvalue=np.nan
            )
            
        return (self.hdf5["pivot_ids"])

    
    @property
    def W_piv(self):
        
        if "W_piv" not in self.hdf5:
            self.hdf5.require_dataset(
                "W_piv", 
                (self.ndim, 2, self.ndim), 
                np.float32, 
                exact=True,
                fillvalue=np.nan
            )
            
        return (self.hdf5["W_piv"])
    
    @property
    def W(self):
        
        if "W" not in self.hdf5:
            self.hdf5.require_dataset(
                "W", 
                (self.X.shape[0], self.ndim), 
                np.float32, 
                exact=True,
                fillvalue=np.nan
            )
            
        return (self.hdf5["W"])


    @property
    def X(self):
        
        return (self._X)
    
    @X.setter
    def X(self, value):
        
        self._X = value


    @property
    def y(self):        

        return (self.hdf5["y"])
    
    @y.setter
    def y(self, value):
        if "y" not in self.hdf5:
            self.hdf5.create_dataset("y", data=value)
        else:
            raise (AttributeError("Attribute already initialized."))


    def _choose_pivots(self, nproc=None):
        """
        A heuristic algorithm to choose distant pivot objects adapted
        from Faloutsos and Lin (1995).
        """
        
        forbidden = self.pivot_ids[:self._ihyprpln].flatten()
        
        while True:
            _jobj = np.random.choice(np.argwhere(self.y[:] == 1).flatten())
            if _jobj not in forbidden:
                break
        
        iobj, jobj = None, None
        while True:
            furthest = self.furthest(_jobj, label=0, nproc=nproc)
            for _iobj in furthest:
                if _iobj not in forbidden:
                    break

            furthest = self.furthest(_iobj, label=1, nproc=nproc)        
            for _jobj in furthest:
                if _jobj not in forbidden:
                    break
            
            if _iobj == iobj and _jobj == jobj:
                break
            else:
                iobj, jobj = _iobj, _jobj
                
        dist = self._distance(self.X[iobj], self.X[jobj])
        print(f"Furtherst distance: {dist:.6f}")

        return (iobj, jobj)
    

    def _init_hdf5(self, path):
        """
        Initialize the HDF5 backend to store pivot objects and images
        of training data.
        
        Arguments:
        - path: pathlib.Path
            The path to the backend. Open as read-only if it already;
            exists; as read/write otherwise.
        """
        
        self._hdf5 = h5py.File(path, mode="a")
        code = np.void(marshal.dumps(self._distance.__code__))
        self._hdf5.attrs["distance"] = code
        self._hdf5.attrs["ndim"] = self.ndim

        return (True)
    

    def distance(self, iobj, jobj, X1=None, X2=None, W1=None, W2=None):
        """
        Return the distance between object at index iobj and object at
        index jobj on the ihyprpln^th hyperplane.
        
        Arguments:
        - iobj: int
            Index of first object to consider.
        - jobj: int
            Index of second object to consider.
        
        Keyword arguments:
        - ihyprpln: int=0
            Index of hyperplane on which to compute distance.
        """
        
        if X1 is None:
            X1 = self.X
        if X2 is None:
            X2 = self.X
        if W1 is None:
            W1 = self.W
        if W2 is None:
            W2 = self.W

        dist = self._distance(X1[iobj], X2[jobj])
                    
        for i in range(self._ihyprpln):
            if dist**2 < (W1[iobj, i] - W2[jobj, i])**2:
                return (0)

            dist = np.sqrt(dist**2 - (W1[iobj, i] - W2[jobj, i])**2)

        return (dist)


    def embed(self, X, nproc=None):
        """
        Return the embedding (images) of the given objects, `X`.
        """
        
        nobj = X.shape[0]
        kobj = np.arange(nobj)
        W = np.zeros((nobj, self.ndim), dtype=np.float32)
        
        for self._ihyprpln in range(self.ndim):
            
            Xpiv = self.X_piv[self._ihyprpln]
            Wpiv = self.W_piv[self._ihyprpln]
            
            d_ij = self.distance(0, 1, X1=Xpiv, X2=Xpiv, W1=Wpiv, W2=Wpiv)
            d_ik = self.pdist(0, kobj, X1=Xpiv, X2=X, W1=Wpiv, W2=W, nproc=nproc)
            d_jk = self.pdist(1, kobj, X1=Xpiv, X2=X, W1=Wpiv, W2=W, nproc=nproc)
            
            W[:, self._ihyprpln]  = np.square(d_ik)
            W[:, self._ihyprpln] += np.square(d_ij)
            W[:, self._ihyprpln] -= np.square(d_jk)
            W[:, self._ihyprpln] /= (d_ij * 2)
            
        return (W)


    def embed_database(self, nproc=None):
        """
        Compute and store the image of every object in the database.
        """
        
        n = self.X.shape[0]
        
        for self._ihyprpln in tqdm.tqdm(range(self.ndim)):

            ipiv, jpiv = self._choose_pivots(nproc=nproc)
            self.pivot_ids[self._ihyprpln] = [ipiv, jpiv]
            self.X_piv[self._ihyprpln, 0] = self.X[ipiv]
            self.X_piv[self._ihyprpln, 1] = self.X[jpiv]
            d_ij = self.distance(ipiv, jpiv)
            
            # if d_ij == 0:
            #     print(ipiv, jpiv)
            
            d  = np.square(self.pdist(np.arange(n), ipiv, nproc=nproc))
            d -= np.square(self.pdist(np.arange(n), jpiv, nproc=nproc))
            d += d_ij ** 2
            d /= (2 * d_ij)
            ####
            d[d < 0] = 0
            ####
            self.W[:, self._ihyprpln] = d
        
        for idim, (ipiv, jpiv) in enumerate(self.pivot_ids):
            self.W_piv[idim, 0] = self.W[ipiv]
            self.W_piv[idim, 1] = self.W[jpiv]

        return (True)
    
    def fit(
        self,
        X, y,
        kernel=("linear", "rbf"), 
        C=[2**n for n in range(-4, 4)],
        gamma=[2**n for n in range(-4, 4)],
        nproc=None
    ):
        self.X = X
        self.y = y
        self.embed_database(nproc=nproc)
        
        params = dict(kernel=kernel, C=C, gamma=gamma)
        svc = sklearn.svm.SVC(probability=True)
        clf = sklearn.model_selection.GridSearchCV(svc, params, n_jobs=nproc)
        clf.fit(self.W[:], self.y[:])
        self._clf = clf.best_estimator_
        
        self.hdf5.create_dataset("clf", data=np.void(pickle.dumps(self._clf)))

    
    def furthest(self, iobj, label=None, nproc=None):
        """
        Return the index of the object furthest from object with index 
        *iobj*.
        """
        if label is None:
            idxs = np.arange(self.y.shape[0])
        else:
            idxs = np.argwhere(self.y[:] == label).flatten()
        
        return (idxs[np.argsort(self.pdist(iobj, idxs, nproc=nproc))[-1::-1]])
    

    def load(path):
        self = FastMapSVM.__new__(FastMapSVM)
        self._hdf5 = h5py.File(path, mode="a")
        self._distance = distance
        self._ihyprpln = 0
        self._ndim = self.hdf5.attrs["ndim"]
        
        self._distance = types.FunctionType(
            marshal.loads(self.hdf5.attrs["distance"]), 
            globals(), 
            "distance"
        )

        self._clf = pickle.loads(np.void(self.hdf5["clf"]))
        
        return (self)


    def pdist(self, iobj, jobj, X1=None, X2=None, W1=None, W2=None, nproc=None):

        iobj = np.atleast_1d(iobj)
        jobj = np.atleast_1d(jobj)
        
        if X1 is None:
            X1 = self.X
        if X2 is None:
            X2 = self.X
        if W1 is None:
            W1 = self.W
        if W2 is None:
            W2 = self.W

        with mp.Pool(processes=nproc, initializer=_init_pdist, initargs=(self, X1, X2, W1, W2)) as pool:
            iterator = itertools.product(iobj, jobj)
                      
            return (np.array(pool.starmap(_pdist, iterator)))


    def predict(self, X, return_image=False, nproc=None):
        
        W = self.embed(X, nproc=nproc)
        
        if return_image is True:
            return (W, self._clf.predict(W))
        else:
            return (self._clf.predict(W))


    def predict_proba(self, X, return_image=False, nproc=None):
        
        W = self.embed(X, nproc=nproc)
        
        if return_image is True:
            return (W, self._clf.predict_proba(W))
        else:
            return (self._clf.predict_proba(W))