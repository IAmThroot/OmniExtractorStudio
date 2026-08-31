import pytest
from omniextract.ai.providers import get_available_providers, get_preferred_provider, get_provider_status, ProviderStatus

def test_get_available_providers():
    pytest.importorskip('onnxruntime')
    providers = get_available_providers()
    assert isinstance(providers, list)

def test_get_preferred_provider_cpu():
    pytest.importorskip('onnxruntime')
    providers = get_preferred_provider('cpu')
    assert isinstance(providers, list)
    if providers:
        assert providers == ['CPUExecutionProvider']

def test_get_preferred_provider_auto():
    pytest.importorskip('onnxruntime')
    providers = get_preferred_provider('auto')
    assert isinstance(providers, list)
    assert len(providers) > 0

def test_get_provider_status():
    status = get_provider_status()
    assert isinstance(status, ProviderStatus)
