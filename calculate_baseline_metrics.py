#!/usr/bin/env python

#
# Copyright (c) 2019, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.
#

"""
calculate_baseline_metrics.py:
    Takes noisy data and ground truth, returns metrics

Workflow:
   1. Read labels from .h5 file
   2. Read noisy data from .h5 or .bw file
   2. Calculate metrics
   3. If required, classification metrics at multiple thresholds.
"""

# Import requirements
import argparse
import numpy as np
import h5py
import torch
import pyBigWig
import pandas as pd
import os
import logging

from claragenomics.dl4atac.metrics import BCE, MSE, Recall, Precision, Specificity, CorrCoef, Accuracy, AUROC, AUPRC, SpearmanCorrCoef, F1
from claragenomics.io.bigwigio import extract_bigwig_intervals
from claragenomics.io.bedio import read_intervals, read_sizes

# Set up logging
log_formatter = logging.Formatter(
    '%(levelname)s:%(asctime)s:%(name)s] %(message)s')
_logger = logging.getLogger('AtacWorks-calculate-metrics')
_handler = logging.StreamHandler()
_handler.setLevel(logging.INFO)
_handler.setFormatter(log_formatter)
_logger.setLevel(logging.INFO)
_logger.addHandler(_handler)


def calculate_class_nums(x, threshold=0.5, message='Bases per class'):
    """
    Function to print number of elements of each class in data.
    Args:
        x (NumPy array): data
        threshold (float): threshold above which to call peak
    Returns:
        number of elements in each class
    """
    nums = {}
    nums['positive'] = (x > threshold).sum()
    nums['negative'] = (1 - (x > threshold)).sum()
    result_str = message + ": " + \
        " | ".join([key + ": {:0.0f}".format(value)
                    for key, value in nums.items()])
    print(result_str)


def h5_to_array(h5file, channel, pad):
    """
    Function to read test data into a NumPy array.
    Args:
        h5file(str): path to hdf5 file containing batched data
        channel(int): channel of hdf5 file to read
        pad(int): interval padding in h5 file
    Returns:
        data: NumPy array containing a channel of the data
    """
    with h5py.File(h5file, 'r') as f:
        if f['data'].shape[2] == 1:
            channel = 0
        data = f['data'][:, :, channel]
    # ignore padding
    if pad is not None:
        center = range(pad, data.shape[1] - pad)
        print("Removing padding and reducing interval size from {} to {}".format(data.shape[1], len(center)))
        data = data[:, center]
    # Flatten data
    data = data.flatten()
    return data


def read_data_file(filename, channel=None, intervals=None, pad=None, dtype='float32'):
    """
    Function to read clean and noisy data for evaluation
    Args:
        filename: path to file
        channel: channel to read if file is an hdf5 file with labels
        intervals: intervals to read if file is in bigWig format
        pad(int): interval padding in h5 file
        dtype(str): numpy dtype to return
    Returns:
        Data as a NumPy array
    """
    if os.path.splitext(filename)[1] == '.h5':
        data = h5_to_array(filename, channel, pad)
        data = data.astype(dtype)
    elif os.path.splitext(filename)[1] == '.bw':
        data = extract_bigwig_intervals(intervals, filename, stack=False, dtype=dtype)
        data = np.concatenate(data)
    # TODO: Error if file extension is neither .h5 nor .bw
    return data


def calculate_metrics(metrics, x, y):
    for metric in metrics:
        metric(x, y)
    return metrics


def parse_args():
    parser = argparse.ArgumentParser(
        description='AtacWorks script to calculate metrics on batched data.')
    parser.add_argument('--label_file', type=str,
                        help='Path to hdf5/bw file containing labels')
    parser.add_argument('--test_file', type=str,
                        help='Path to hdf5/bw file containing labels. If not provided, assumed to be present in label_file.')
    parser.add_argument('--peak_file', type=str,
                        help='Path to hdf5/bw file containing peak labels. If not provided, assumed to be present in label_file.')
    parser.add_argument('--task', type=str, choices=('regression',
                                                   'classification'), help='determines metrics')
    parser.add_argument('--ratio', type=float, help='subsampling ratio')
    parser.add_argument('--sep_peaks', action='store_true',
                        help='separate regression metrics for peaks and non-peaks')
    parser.add_argument('--thresholds', type=str,
                        help='threshold or list of thresholds for classification metrics')
    parser.add_argument('--auc', action='store_true', help='calculate AUC metrics')
    parser.add_argument('--intervals', type=str,
                        help='Intervals to read bigWig files')
    parser.add_argument('--sizes', type=str,
                        help='Chromosome sizes to read bigWig file')
    parser.add_argument('--pad', type=int, help='interval padding in label h5 file')
    args = parser.parse_args()
    return args


args = parse_args()

# Load intervals if supplied
_logger.info('Loading intervals')
if args.intervals is not None:
    intervals = read_intervals(args.intervals)
# If not, use whole chromosome lengths
elif args.sizes is not None:
    intervals = read_sizes(args.sizes, as_intervals=True)
else:
    intervals = None


# Calculate regression metrics
if args.task == 'regression':

    # Load labels
    _logger.info("Loading labels for regression") 
    y = read_data_file(args.label_file, 1, intervals, pad=args.pad)

    # Load data
    _logger.info("Loading data for regression")
    if args.test_file is None:
        x = read_data_file(args.label_file, 0, pad=args.pad)
    else:
        x = read_data_file(args.test_file, 0, intervals)

    # Calculate metrics
    _logger.info("Calculating metrics for regression")
    metrics = calculate_metrics([MSE(), CorrCoef(), SpearmanCorrCoef()], x, y)
    print("Regression metrics on full data : " +
          " | ".join([str(metric) for metric in metrics]))

    if args.ratio:
        metrics = calculate_metrics([MSE()], x/args.ratio, y)
        print("MSE for data/subsampling ratio : " +
              " | ".join([str(metric) for metric in metrics]))

    if args.sep_peaks:
        # Load peak labels
        _logger.info("Loading labels for classification")
        if args.peak_file is not None:
            y_peaks = read_data_file(args.peak_file, 2, intervals, pad=args.pad)
        else:
            y_peaks = read_data_file(args.label_file, 2, intervals, pad=args.pad)

        # Calculate separate metrics for peak and non-peak regions
        _logger.info("Calculating metrics for regression in peaks")
        metrics = calculate_metrics(
            [MSE(), CorrCoef(), SpearmanCorrCoef()], x[y_peaks == 1], y[y_peaks == 1])
        print("Regression metrics in peaks : " +
              " | ".join([str(metric) for metric in metrics]))
        _logger.info("Calculating metrics for regression outside peaks")
        metrics = calculate_metrics(
            [MSE(), CorrCoef(), SpearmanCorrCoef()], x[y_peaks == 0], y[y_peaks == 0])
        print("Regression metrics outside peaks : " +
              " | ".join([str(metric) for metric in metrics]))


# Calculate classification metrics
else:

    # Load labels
    _logger.info("Loading labels for classification")
    y_peaks = read_data_file(args.label_file, 2, intervals, pad=args.pad, dtype='int8')

    # Load data
    _logger.info("Loading data for classification")
    if args.thresholds is not None:
        x_peaks = read_data_file(args.test_file, 1, intervals, dtype='float32')
        # fp32 is required by torch for sensitivity/specificity calculation
    else:
        x_peaks = read_data_file(args.test_file, 1, intervals, dtype='float16')

    # Calculate number of bases in peaks
    calculate_class_nums(y_peaks, message="Bases per class in clean data")

    # Get threshold for evaluation
    if args.thresholds is not None:
        thresholds = args.thresholds.strip("[]")
        if thresholds == args.thresholds:
            # Only one threshold provided
            thresholds = [float(thresholds)]
        else:
            # Multiple thresholds provided
            thresholds = [float(t.strip()) for t in thresholds.split(',')]

        # Calculate metrics for each threshold
        _logger.info("Calculating per-threshold classification metrics")
        for t in thresholds:
            calculate_class_nums(
                x_peaks, t, message="Bases per class at threshold {}".format(t))
            metrics = calculate_metrics([Recall(t), Precision(
                t), Specificity(t), Accuracy(t), F1(t)], x_peaks, y_peaks)
            print("Classification metrics at threshold {}".format(t) +
                  " : " + " | ".join([str(metric) for metric in metrics]))

    # Calculate AUC
    if args.auc is not None:
        _logger.info("Calculating AUC metrics")
        metrics = calculate_metrics([AUROC(), AUPRC()], x_peaks, y_peaks)
        print("AUC metrics: " + " | ".join([str(metric) for metric in metrics]))

_logger.info('Done!') 
