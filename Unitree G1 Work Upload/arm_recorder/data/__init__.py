"""Data + ML package for the G-1 arm-direction MLP.

Modules
-------
train         : train an ``MLPRegressor`` from recorded CSV samples.
inference_arm : load a trained bundle and predict end joint angles.

The recorder (``arm_train_recorder.py``) writes samples to
``data/arms/<arm>/training_data_with_waist.csv``; ``train.py`` turns those into
``data/artifacts/<arm>-arm/arm_mlp.joblib``; ``run_geoff_gui.py`` loads that
bundle via :func:`inference_arm.load_bundle` and drives the arm with arrow keys.
"""
