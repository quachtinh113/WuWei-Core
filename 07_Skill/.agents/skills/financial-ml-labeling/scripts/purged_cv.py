#!/usr/bin/env python3
"""Purged & Embargoed Time Series Cross-Validation Engine.

Based on Marcos López de Prado, 'Advances in Financial Machine Learning' (Chapter 7).
Eliminates:
1. Look-Ahead Bias & Information Leakage from standard K-Fold / StratifiedKFold.
2. Label overlap contamination via Purge Gap: Drops train samples whose holding interval
   [t0, t1] overlaps with the test evaluation window.
3. Serial correlation leakage via Embargo: Drops train samples immediately following
   the test set by an embargo window h (e.g., 1% of sample size or 24 hours).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Generator, Iterable, List, Optional, Tuple


class PurgedGroupTimeSeriesSplit:
    """Marcos López de Prado Purged & Embargoed Time Series Split.

    Parameters
    ----------
    n_splits : int, default=5
        Number of splitting folds.
    max_train_group_size : Optional[int], default=None
        Maximum number of groups in each training split.
    group_gap : int, default=0
        Number of groups to leave between train and test.
    embargo_pct : float, default=0.01
        Percentage of total observations to embargo immediately after test set.
    """

    def __init__(
        self,
        n_splits: int = 5,
        max_train_group_size: Optional[int] = None,
        group_gap: int = 0,
        embargo_pct: float = 0.01,
    ):
        self.n_splits = n_splits
        self.max_train_group_size = max_train_group_size
        self.group_gap = group_gap
        self.embargo_pct = embargo_pct

    def split(
        self,
        X: pd.DataFrame,
        y: Optional[pd.Series] = None,
        groups: Optional[Iterable] = None,
        t1: Optional[pd.Series] = None,
    ) -> Generator[Tuple[np.ndarray, np.ndarray], None, None]:
        """Generate indices to split data into training and test set.

        Parameters
        ----------
        X : pd.DataFrame
            Features indexed by timestamp or sorted chronologically.
        y : Optional[pd.Series]
            Target labels.
        groups : Optional[Iterable]
            Grouping array (e.g., date groups).
        t1 : Optional[pd.Series]
            Series with index = event start timestamp, value = vertical/touch timestamp
            indicating the end of the label evaluation window (from Triple Barrier).

        Yields
        ------
        train_idx, test_idx : Tuple[np.ndarray, np.ndarray]
            The training and testing set indices for each fold.
        """
        n_samples = len(X)
        indices = np.arange(n_samples)
        embargo_len = int(n_samples * self.embargo_pct)

        # Calculate test chunk sizes for expanding window walk-forward
        test_size = n_samples // (self.n_splits + 1)

        for i in range(self.n_splits):
            test_start = (i + 1) * test_size
            test_end = (i + 2) * test_size if i < self.n_splits - 1 else n_samples
            test_indices = indices[test_start:test_end]

            # Initial training window prior to test set
            train_end = test_start - self.group_gap
            if train_end <= 0:
                continue

            train_start = 0
            if self.max_train_group_size is not None:
                train_start = max(0, train_end - self.max_train_group_size)

            train_indices = indices[train_start:train_end]

            # Apply Purging if label end times (t1) are provided
            if t1 is not None and isinstance(X.index, pd.DatetimeIndex):
                test_start_dt = X.index[test_start]
                test_end_dt = X.index[test_end - 1]

                # Purge training events that overlap with test evaluation window
                t1_train = t1.iloc[train_indices]
                train_start_dt = X.index[train_indices]

                # Sample i in train overlaps test if:
                # t0_train <= test_end_dt and t1_train >= test_start_dt
                overlap_mask = (train_start_dt <= test_end_dt) & (t1_train >= test_start_dt)
                train_indices = train_indices[~overlap_mask.values]

            # Apply Embargo after test set if training also includes post-test data
            # (In walk-forward TimeSeriesSplit, train is strictly pre-test so embargo applies to future splits)
            yield train_indices, test_indices


class PurgedKFold:
    """Marcos López de Prado Purged K-Fold Cross-Validator with Embargo.
    
    Splits observations into K folds, purging overlapping labels across both
    pre-test and post-test training partitions.
    """

    def __init__(
        self,
        n_splits: int = 5,
        t1: Optional[pd.Series] = None,
        pct_embargo: float = 0.01,
    ):
        self.n_splits = n_splits
        self.t1 = t1
        self.pct_embargo = pct_embargo

    def split(
        self,
        X: pd.DataFrame,
        y: Optional[pd.Series] = None,
        groups: Optional[Iterable] = None,
    ) -> Generator[Tuple[np.ndarray, np.ndarray], None, None]:
        n_samples = len(X)
        indices = np.arange(n_samples)
        embargo = int(n_samples * self.pct_embargo)
        fold_bounds = [(i * n_samples // self.n_splits, (i + 1) * n_samples // self.n_splits) for i in range(self.n_splits)]

        for test_start, test_end in fold_bounds:
            test_indices = indices[test_start:test_end]
            
            # Post-test embargo
            post_test_start = min(n_samples, test_end + embargo)
            
            train_pre = indices[:test_start]
            train_post = indices[post_test_start:]
            train_indices = np.concatenate([train_pre, train_post])

            if self.t1 is not None and isinstance(X.index, pd.DatetimeIndex):
                test_t0 = X.index[test_start]
                test_t1 = self.t1.iloc[test_end - 1] if test_end - 1 < len(self.t1) else X.index[-1]
                
                # Purge: t1_train >= test_t0 and t0_train <= test_t1
                t0_train = X.index[train_indices]
                t1_train = self.t1.iloc[train_indices]
                overlap = (t0_train <= test_t1) & (t1_train >= test_t0)
                train_indices = train_indices[~overlap.values]

            yield train_indices, test_indices

