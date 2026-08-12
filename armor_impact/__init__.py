"""LS-DYNA armor impact screening pipeline."""

from .api import InjuryPredictionError, predict_injury

__all__ = ["InjuryPredictionError", "predict_injury"]
__version__ = "0.3.0"
