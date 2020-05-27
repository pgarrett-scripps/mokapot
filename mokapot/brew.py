"""
Defines a function to run the Percolator algorithm.
"""
import logging
import copy
from typing import Tuple
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from mokapot.model import Model
from mokapot.dataset import PsmDataset
from mokapot.confidence import PsmConfidence

LOGGER = logging.getLogger(__name__)

# Functions -------------------------------------------------------------------
def brew(psms: PsmDataset,
         model: Model = Model(),
         train_fdr: float = 0.01,
         test_fdr: float = 0.01,
         max_iter: int = 10,
         folds: int = 3,
         max_workers: int = 1) \
         -> Tuple[PsmConfidence, Tuple[Model, ...]]:
    """
    Analyze a collection of PSMs using the Percolator algorithm.

    The provided PSMs analyzed using the Percolator algorithm using
    cross-validation to evaluate the held-out PSMs. If a multiple
    collections of PSMs are provided, PSM, peptide, and protein-level
    FDR estimates are returned for each collection.

    Parameters
    ----------
    psms
        One or more PsmDataset objects. PSMs are aggregated across
        all of the collections for model training.

    model
        The model to be fit. The default attempts to mimic the same
        support vector machine models used by Percolator.

    train_fdr
        The false-discovery rate threshold to define positive examples
        during model training.

    test_fdr
        The false-discovery rate threshold to evaluate whether the
        learned models yield more PSMs than the best feature.

    max_iter
        The maximum number of iterations to use for training.

    folds
        The number of cross-validation folds to use. PSMs originating
        from the same mass spectrum are always in the same fold.

    max_workers
        The number of processes to use for model training. More workers
        will require more memory, but will typically decrease the total
        run time. An integer exceeding the number of folds will have
        no additional effect.

    Returns
    -------
    A tuple containing a PsmConfidence object and the trained
    Classifier objects. The PsmConfidence object contains
    false-discovery rate estimates for the PSMs, peptides,
    and proteins.
    """
    all_idx = set(range(len(psms.data)))
    test_idx = psms.split(folds)

    train_sets = [psms.data.iloc[tuple(all_idx - set(i))] for i in test_idx]
    test_sets = [psms.data.iloc[i] for i in test_idx]

    # Create args for map:
    map_args = [_fit_model,
                train_sets,
                [copy.deepcopy(model) for _ in range(folds)],
                [train_fdr]*folds,
                [max_iter]*folds,
                list(range(1, folds+1))]

    # Train models in parallel
    with ProcessPoolExecutor(max_workers=max_workers) as prc:
        if max_workers == 1:
            map_fun = map
        else:
            map_fun = prc.map

        models = [c for c in map_fun(*map_args)]

    scores = [_predict(p, m, test_fdr) for p, m in zip(models, test_sets)]
    test_idx = sum(test_idx, tuple())
    scores = np.concatenate(scores)[test_idx]

    return psms.assign_confidence(scores, None, desc=True)


# Utility Functions -----------------------------------------------------------
def _predict(psms: PsmDataset, model: Model, test_fdr: float):
    """Return calibrated scores for the PSMs"""
    return psms.calibrate_scores(model.predict(psms), fdr_threshold=test_fdr)

def _fit_model(train_set: PsmDataset, model: Model, train_fdr: float,
               max_iter: int) -> Model:
    """
    Fit the estimator using the training data.

    Parameters
    ----------
    train_set
        A PsmDataset that specifies the training data

    estimator
        A Classifier to train.

    train_fdr
        The FDR threshold used to define positive examples during the
        Percolator algorithm.

    max_iter
        The maximum number of iterations to run the algorithm.

    fold
        The fold identifier for this set. This is just used for
        messages.
    """
    model.fit(train_set, train_fdr=train_fdr, max_iter=max_iter)
    return model
