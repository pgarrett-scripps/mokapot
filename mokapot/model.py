"""
This module defines the model classes to used mokapot.
"""
import logging

import numpy as np
import sklearn.base as base
import sklearn.svm as svm
import sklearn.model_selection as ms
import sklearn.preprocessing as pp
from sklearn.exceptions import NotFittedError

from .dataset import PsmDataset

LOGGER = logging.getLogger(__name__)

# Constants -------------------------------------------------------------------
PERC_GRID = {"class_weight": [{0: neg, 1: pos}
                              for neg in (0.1, 1, 10)
                              for pos in (0.1, 1, 10)]}


# Classes ---------------------------------------------------------------------
class Model():
    """
    A machine learning model to re-score PSMs.

    A linear support vector machine (SVM) model is used by default in an
    attempt emulate the SVM models in Percolator. Alternatively, any
    classifier with a scikit-learn estimator interface can be used,
    assuming that it defines the canonical `fit()` and
    `decision_function()` methods. This class also supports hyper
    parameter optimization using the scikit-learn `GridSearchCV` and
    `RandomizedSearchCV` classes.

    Parameters
    ----------
    estimator : classifier object, optional
        A classifier that is assumed to implement the scikit-learn
        estimator interface.
    scaler : scaler object or "as-is", optional
        Defines how features are normalized before model fitting and
        prediction. The default, None, subtracts the mean and scales
        to unit variance using `sklearn.preprocessing.StandardScaler`.
        Other scalers should follow the scikit-learn transformer
        interface, implementing `fit_transform` and `transform` methods.
        Alternatively, the string "as-is" leaves the features in
        their original scale.

    Attributes
    ----------
    estimator : classifier object
        The classifier used to re-score PSMs.
    scaler : scaler object
        The scaler used to normalize features.
    features : list of str or None
        The features used to fit the model. None if the model has yet
        to be trained.
    is_trained : bool
        Indicates if the model has bee trained.
    """
    def __init__(self, estimator=None, scaler=None):
        """Initialize a Model object"""
        if estimator is None:
            svm_model = svm.LinearSVC(dual=False)
            estimator = ms.GridSearchCV(svm_model, param_grid=PERC_GRID,
                                        refit=False,
                                        cv=3)

        self.estimator = base.clone(estimator)
        self.features = None
        self.is_trained = False
        self._base_params = self.estimator.get_params()

        if scaler == "as-is":
            self.scaler = DummyScaler()
        elif scaler is None:
            self.scaler = pp.StandardScaler()
        else:
            self.scaler = base.clone(scaler)


    def decision_function(self, psms):
        """
        Score a collection of PSMs

        Parameters
        ----------
        psms : PsmDataset object
            The collection of PSMs to score.

        Returns
        -------
        numpy.ndarray
            A vector containing the score for each PSM.
        """
        if not self.is_trained:
            raise NotFittedError("This model is untrained. Run fit() first.")

        feat_names = psms.features.columns.tolist()
        if set(feat_names) != set(self.features):
            raise ValueError("Features of the input data do not match the "
                             "features of this Model.")

        feat = self.scaler.transform(psms.features.loc[:, self.features].values)
        return self.estimator.decision_function(feat)

    def predict(self, psms):
        """Alias for `decision_function()`."""
        return self.decision_function(psms)

    def fit(self, psms, train_fdr=0.01, max_iter=10, direction=None):
        """
        Fit the machine learning model using the Percolator algorithm.

        The model if trained by iteratively learning to separate decoy
        PSMs from high-scoring target PSMs. By default, an initial
        direction is chosen as the feature that best separates target
        from decoy PSMs. A false discovery rate threshold is used to
        define how high a target must score to be used as a positive
        example in the next training iteration.

        Parameters
        ----------
        psms : PsmDataset object
            A collection of PSMs from which to train the model.
        train_fdr : float, optional
            The maximum false discovery rate at which to consider a
            target PSM as a positive example.
        max_iter : int, optional
            The number of iterations to perform.
        direction : str or None, optional
            The name of the feature to use as the initial direction for
            ranking PSMs. The default, None, automatically selects the
            feature that finds the most PSMs below the `train_fdr`. This
            will be ignored in the case the model is already trained.
        """
        # Choose the initial direction
        LOGGER.info("Finding initial direction...")
        best_feat, feat_pass, feat_labels = psms._find_best_feature(train_fdr)
        if direction is None and not self.is_trained:
            LOGGER.info("  - Selected feature %s with %i PSMs at q<=%g.",
                        best_feat, feat_pass, train_fdr)
            start_labels = feat_labels
        elif self.is_trained:
            scores = self.estimator.decision_function(psms)
            start_labels = psms._update_labels(scores, fdr_threshold=train_fdr)
            LOGGER.info("  - The pretrained model found %i PSMs at q<=%g",
                        (start_labels == 1).sum(), train_fdr)
        else:
            desc_labels = psms._update_labels(psms.features[direction].values)
            asc_labels = psms._update_labels(psms.features[direction].values,
                                             desc=False)
            if (desc_labels == 1).sum() >= (asc_labels == 1).sum():
                start_labels = desc_labels
            else:
                start_labels = asc_labels

        # Normalize Features
        self.features = psms.features.columns.tolist()
        norm_feat = self.scaler.fit_transform(psms.features)

        # Initialize Model and Training Variables
        if hasattr(self.estimator, "estimator"):
            LOGGER.info("Selecting hyperparameters...")
            cv_samples = norm_feat[feat_labels.astype(bool), :]
            cv_targ = (feat_labels[feat_labels.astype(bool)]+1)/2
            self.estimator.fit(cv_samples, cv_targ)
            best_params = self.estimator.best_params_
            model = self.estimator.estimator
            model.set_params(**best_params)
            LOGGER.info("  - best parameters: %s", best_params)
        else:
            model = self.estimator

        # Begin training loop
        target = start_labels
        num_passed = []
        LOGGER.info("Beginning training loop...")
        for i in range(max_iter):
            # Fit the model
            samples = norm_feat[target.astype(bool), :]
            iter_targ = (target[target.astype(bool)]+1)/2
            model.fit(samples, iter_targ)

            # Update scores
            scores = model.decision_function(norm_feat)

            # Update target
            target = psms._update_labels(scores, fdr_threshold=train_fdr)
            num_passed.append((target == 1).sum())
            LOGGER.info("  - Iteration %i: %i training PSMs passed.",
                        i, num_passed[i])

        self.estimator = model
        self.is_trained = True
        LOGGER.info("Done training.")


class DummyScaler():
    """
    Implements the interface of scikit-learn scalers, but does
    nothing to the data. This simplifies the training code.

    :meta private:
    """
    def fit(self, x):
        pass

    def fit_transform(self, x):
        return x

    def transform(self, x):
        return x
