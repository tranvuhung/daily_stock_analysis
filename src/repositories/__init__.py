# -*- coding: utf-8 -*-
"""
===================================
Data access layer package initialisation
===================================

Responsibilities:
1. Export all Repository classes
"""

from src.repositories.analysis_repo import AnalysisRepository
from src.repositories.backtest_repo import BacktestRepository
from src.repositories.decision_signal_repo import DecisionSignalRepository
from src.repositories.decision_signal_outcome_repo import DecisionSignalOutcomeRepository
from src.repositories.stock_repo import StockRepository
from src.repositories.skill_opinion_sample_repo import SkillOpinionSampleRepository

__all__ = [
    "AnalysisRepository",
    "BacktestRepository",
    "DecisionSignalRepository",
    "DecisionSignalOutcomeRepository",
    "StockRepository",
    "SkillOpinionSampleRepository",
]
