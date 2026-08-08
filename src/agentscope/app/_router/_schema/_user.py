# -*- coding: utf-8 -*-
"""Request / response schemas for user settings."""

from ...storage import GlobalDefaultModels


class UserModelDefaultsResponse(GlobalDefaultModels):
    """Response body for fetching the current user's default models."""


class UpdateUserModelDefaultsRequest(GlobalDefaultModels):
    """Request body for replacing the current user's default models."""
