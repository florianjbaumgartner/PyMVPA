import cPickle as pickle
import volgeom
from collections import Mapping

import nibabel as ni, numpy as np
import collections
import volgeom
import utils
import operator

from mvpa2.misc.neighborhood import IndexQueryEngine
from mvpa2.measures.searchlight import Searchlight

'''
This class is intended for general use of storing sparse attributes
associated with keys in a dictionary. 

Instantiation requires a set of labels (called sa_labels to mimick Dataset)
and every entry added has to be a dict with the keys matching sa_labels.  
'''
class SparseAttributes(object):
    def __init__(self, sa_labels):
        self._sa_labels = list(sa_labels)
        self.sa = dict()
        self.a = dict()

    def set(self, roi_label, roi_attrs_dict):
        if not roi_attrs_dict:
            roi_attrs_dict = None
        else:
            if not type(roi_attrs_dict) is dict:
                raise TypeError("roi attributes should be a dict, but is %r:\n%r" %
                                (type(roi_attrs_dict), roi_attrs_dict))

            # if sa_labels is set, check it has all the keys
            if set(self._sa_labels) != set(roi_attrs_dict):
                raise ValueError("Key set mismatch: %r != %r" %
                                 (self._sa_labels, roi_attrs_dict))

        self.sa[roi_label] = roi_attrs_dict

    def add(self, roi_label, roi_attrs_dict):
        if roi_label in self.sa.keys():
            raise ValueError("name clash: key %s already present" % roi_label)
        self.set(roi_label, roi_attrs_dict)

    def sa_labels(self):
        return list(self._sa_labels)

    def keys(self):
        return filter(lambda x : not self.sa[x] is None, self.all_keys())

    def all_keys(self):
        return self.sa.keys()

    def get(self, roi_label, sa_label):
        roiattr = self.sa[roi_label]
        return roiattr[sa_label] if roiattr else None

    def get_tuple(self, roi_label, sa_labels=None):
        if sa_labels is None:
            sa_labels = tuple(self._sa_labels)

        roiattr = self.sa[roi_label]
        if not roiattr:
            return None

        vs = [roiattr[sa_label] for sa_label in sa_labels]

        return zip(*vs)

    def get_attr_mapping(self, roi_attr):
        '''Provides a dict-like object with lookup for a 
        single ROI attribute'''

        if not roi_attr in self.sa_labels():
            raise KeyError("attribute %r not in map" % roi_attr)

        return AttrMapping(self, roi_attr)

    def get_inv_attr_mapping(self, roi_attr):
        '''Provides an inverse mapping from get_attr_mapping
        This mapping is computed and returned as a new dict
        '''
        if isinstance(roi_attr, AttrMapping):
            forward = roi_attr
        else:
            forward = self.get_attr_mapping(roi_attr)

        backward = dict()
        for k, vs in forward.iteritems():
            for v in vs:
                if not v in backward:
                    backward[v] = []
                backward[v].append(k)

        return backward


    def __repr__(self):
        return ("SparseAttributes with %i entries, %i labels (%r)\nGeneral attributes: %r" %
                (len(self.sa), len(self._sa_labels), self._sa_labels, self.a.keys()))

    def __eq__(self, other):
        if not isinstance(other, SparseAttributes):
            return False

        if set(self.keys()) != set(other.keys()):
            return False
        if set(self.all_keys()) != set(other.all_keys()):
            return False
        if set(self.sa_labels()) != set(other.sa_labels()):
            return False

        labs = self.sa_labels()
        for k in self.keys():
            if self.get_tuple(k, labs) != other.get_tuple(k, labs):
                return False

        return True



class AttrMapping(Mapping):
    def __init__(self, cls, roi_attr):
        self._roi_attr = roi_attr
        self._cls = cls

    def __getitem__(self, key):
        return self._cls.get(key, self._roi_attr)

    def __len__(self):
        return len(self.__keys__())

    def __keys__(self):
        return list(self._cls.keys())

    def __iter__(self):
        return iter(self.__keys__())

def paired_common_attributes_count(sp_attrs, roi_attr):
    '''function to count how often the same attribute is shared across keys in
    the input sparse_attributes. Useful to count how many voxels are shared across 
    center nodes'''
    inv = sp_attrs.get_inv_attr_mapping(roi_attr)
    swp = lambda x, y: (x, y) if x < y else (y, x)

    d = dict()
    for k, vs in inv.iteritems():
        for i, x in enumerate(vs):
            for j, y in enumerate(vs):
                if i <= j:
                    continue
                s = swp(x, y)
                if not s in d:
                    d[s] = 0
                d[s] += 1
    return d

def voxel2nearest_node(sp_attrs, sort_by_label=[
                                    'center_distances',
                                    'grey_matter_position'],
                            sort_by_proc=[None, lambda x : abs(x - .5)],
                            return_label='lin_vox_idxs'):

    '''finds for each voxel the nearest node
    
    'nearest' in this sense depends on the criteria specified in the arguments.
    by default distances are first compared based on geodesic distance
    from lines connecting white/pial matter surfaces, and then relative
    position in the grey matter (.5 means just in between the two) 
    
    in a typical use case, the first input argument is the result
    from running voxel_selection
    
    added for swaroop
    
    TODO better documentation
    '''


    all_labels = sort_by_label + [return_label]
    sort_by_count = len(sort_by_label)

    id = lambda x:x # identity function
    if sort_by_proc is None:
        sort_by_proc = [None for _ in xrange(sort_by_count)]

    sort_by_proc = [f or id for f in sort_by_proc]


    sort_by_getter = lambda x: tuple([f(x[i]) for i, f in enumerate(sort_by_proc)])
    sort_by_getter_count = len(sort_by_proc)
    #key_getter = operator.itemgetter(*range(sort_by_count))
    value_getter = operator.itemgetter(sort_by_getter_count) # item after sort_by 

    node_idxs = sp_attrs.keys()
    vox2node_and_attrs = dict()

    for node_idx in node_idxs:
        attr_vals = sp_attrs.get_tuple(node_idx, all_labels)

        for attr_val in attr_vals:
            k, v = sort_by_getter(attr_val), value_getter(attr_val)

            if v in vox2node_and_attrs:
                n, attrs = vox2node_and_attrs[v]
                if k >= attrs:
                    continue

            vox2node_and_attrs[v] = (node_idx, k)

    n2v = dict((k, v[0]) for k, v in vox2node_and_attrs.iteritems())

    return n2v

class SparseVolumeNeighborhood():
    def __init__(self, attr, vg, voxel_ids_label='lin_vox_idxs'):
        mp = attr.get_attr_mapping(voxel_ids_label)
        self._keys = list(mp.keys())
        self._n2vs = dict(mp) # node 2 voxel mapping
        self._volgeom = vg

        self._setup()

    def _setup(self):
        # compute the mask, once and forever
        linmask = np.zeros((self._volgeom.nvoxels,), np.int32)

        for k in self._keys:
            linmask[self._n2vs[k]] = 1

        keys = self._keys
        nkeys = len(keys)
        nmask = np.sum(linmask > 0)

        if nkeys > len(linmask):
            raise ValueError('Unsupported: more centers (%d) than voxels (%d)'
                             % (nkeys, len(linmask)))

        delta = nmask - nkeys
        maskpos = 0
        while delta < 0:
            if linmask[maskpos] == 0:
                linmask[maskpos] = 1
                delta += 1
            maskpos += 1

        self._linmask = linmask

        masknonzero = np.asarray(np.nonzero(linmask)[0][:nkeys])
        maskijk = self._volgeom.lin2ijk(masknonzero)

        self._ijk2vs = dict()
        for i in xrange(nkeys):
            ijk = tuple(maskijk[i, :])
            self._ijk2vs[ijk] = self._n2vs[keys[i]]

        # mapping for center_ids to position in mask
        self._center_ids2maskpos = dict((v, i) for i, v in enumerate(self._keys))

    @property
    def keys(self):
        return list(self._keys)

    @property
    def volgeom(self):
        return self._volgeom

    @property
    def mask(self):
        vg = self._volgeom
        shape = vg.shape[:3]

        mask = np.reshape(self._linmask, shape)

        return ni.Nifti1Image(mask, vg.affine)


    def __call__(self, coordinate):
        center_array = np.asanyarray(coordinate)[np.newaxis][0]
        center_tuple = (center_array[0], center_array[1], center_array[2])

        if not center_tuple in self._ijk2vs:
            raise ValueError('Not in keys: %r' % (center_tuple,))

        lin = self._ijk2vs[center_tuple]
        ijk = self._volgeom.lin2ijk(lin)
        return map(tuple, ijk)

    def searchlight(self, datameasure, center_ids=None,
                space='voxel_indices', **kwargs):
        """Creates a `Searchlight` to run a scalar `Measure` on
        all neighborhoods within a dataset.
        
        The idea for a searchlight algorithm stems from a paper by
        :ref:`Kriegeskorte et al. (2006) <KGB06>`.
        
        This implementation supports surface-based searchlights as well,
        as described in Oosterhof, Wiestler, Downing & Diedrichsen,
        2011, Neuroimage.
        
        Parameters
        ----------
        datameasure : callable
          Any object that takes a :class:`~mvpa2.datasets.base.Dataset`
          and returns some measure when called.
        center_ids : list of int
          List of feature ids (typically node indices, for a surface-based
          searchlight) that serve as neighboorhood identifiers (for the 
          surface-based searchlight, these are the centers of the discs)
        space : str
          Name of a feature attribute of the input dataset that defines the spatial
          coordinates of all features.
        **kwargs
          In addition this class supports all keyword arguments of its
          base-class :class:`~mvpa2.measures.base.Measure`.
        
        Notes
        -----
        If `Searchlight` is used as `SensitivityAnalyzer` one has to make
        sure that the specified scalar `Measure` returns large
        (absolute) values for high sensitivities and small (absolute) values
        for low sensitivities. Especially when using error functions usually
        low values imply high performance and therefore high sensitivity.
        This would in turn result in sensitivity maps that have low
        (absolute) values indicating high sensitivities and this conflicts
        with the intended behavior of a `SensitivityAnalyzer`.
        """
        if center_ids is None:
            center_ids = self.keys

        roi_ids = [self._center_ids2maskpos[center_id] for center_id in center_ids]

        # build a matching query engine from the arguments
        neighborhood = self
        kwa = {space: neighborhood}
        qe = IndexQueryEngine(**kwa)
        # init the searchlight with the queryengine
        return Searchlight(datameasure, queryengine=qe, roi_ids=roi_ids,
                           **kwargs)











class SparseVolumeAttributes(SparseAttributes):
    """
    Sparse attributes with volume geometry. 
    This class can store the result of surface-based voxel selection.
    
    Parameters
    ==========
    sa_labels: list
        labels of attributes stored in this instance
    volgeom: volgeom.VolGeom
        volume geometry
    """
    def __init__(self, sa_labels, volgeom):
        super(self.__class__, self).__init__(sa_labels)
        self.a['volgeom'] = volgeom

    """
    Returns a neighboorhood function
    
    Parameters
    ==========
    mask
        volume mask (usually from get_niftiimage_mask)
    voxel_ids_label
        label of voxel ids of neighboors
    
    
    Returns
    =======
    neighboorhood: SparseNeighborhood 
        neighboorhood function that can be used to access the 
        neighbors for searchlight centers stored in this instance
    """

    def get_volgeom(self):
        return self.a['volgeom']

    """
    Returns
    =======
    volgeom: volgem.VolGeom
        volume geometry stored in this instance
    """

def to_file(fn, a):
    '''
    Stores attributes in a file
    
    Parameters
    ----------
    fn: str
        Output filename
    a: SparseAttributes
        attributes to be stored
    '''

    with open(fn, 'w') as f:
        pickle.dump(a, f, protocol=pickle.HIGHEST_PROTOCOL)

def from_file(fn):
    '''
    Reads attributes from a file
    
    Parameters
    ----------
    fn: str
        Input filename
    
    Returns
    -------
    a: SparseAttributes
        attributes to be stored
    '''

    with open(fn) as f:
        r = pickle.load(f)
    return r


