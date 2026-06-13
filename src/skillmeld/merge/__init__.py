# SPDX-License-Identifier: Apache-2.0
"""The merge engine: parse, dedupe, conflicts, reconcile, prune, partition, synthesize, verify.

Pure functions, network-free and free of model calls. The host Claude supplies judgment by
selecting and labeling existing atoms; this code never invents instruction text.
"""
