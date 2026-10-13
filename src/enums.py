# -*- coding: utf-8 -*-
"""
===================================
Enum type definitions
===================================

Centralizes the enum types used in the system for type safety and readability.
"""

from enum import Enum


class ReportType(str, Enum):
    """
    Report type enum

    Used to select the pushed report format when analysis is triggered via the API.
    Inherits from str so it can be compared and serialized directly as a string.
    """
    SIMPLE = "simple"  # Simple report: uses generate_single_stock_report
    FULL = "full"      # Full report: uses generate_dashboard_report
    BRIEF = "brief"    # Brief mode: 3-5 sentence summary, suited to mobile/push

    @classmethod
    def from_str(cls, value: str) -> "ReportType":
        """
        Safely convert a string to an enum value
        
        Args:
            value: String value
            
        Returns:
            The matching enum value; invalid input returns the default SIMPLE
        """
        try:
            normalized = value.lower().strip()
            if normalized == "detailed":
                normalized = cls.FULL.value
            return cls(normalized)
        except (ValueError, AttributeError):
            return cls.SIMPLE
    
    @property
    def display_name(self) -> str:
        """Get the display name"""
        return {
            ReportType.SIMPLE: "Báo cáo rút gọn",
            ReportType.FULL: "Báo cáo đầy đủ",
            ReportType.BRIEF: "Báo cáo tóm tắt",
        }.get(self, "Báo cáo rút gọn")
