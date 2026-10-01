"""Shared asset extraction; legacy prop endpoints use the same storage and logic."""
from .prop_extraction import AssetExtractionOutput, PropExtractionService


class AssetExtractionService(PropExtractionService):
    def __init__(self, store):
        super().__init__(store, asset_type=None)


__all__ = ['AssetExtractionOutput', 'AssetExtractionService']
