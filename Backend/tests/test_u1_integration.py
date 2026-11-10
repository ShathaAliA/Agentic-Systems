"""Compatibility boundary uses the reviewed Core processor, no database URLs."""
from app.usage import integration, processor


def test_integration_is_only_core_reexports():
    assert integration.run_once is processor.run_once
    assert integration.ProviderReceipt is processor.ProviderReceipt
    assert integration.AccountingContext is processor.AccountingContext
