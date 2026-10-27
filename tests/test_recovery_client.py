import httpx
import pytest

from membrain.client import MemoryClient
from membrain.helpers import uid


@pytest.mark.parametrize('epoch', [None, uid()])
def test_recovery_handshake_never_implicitly_acknowledges_returned_epoch(epoch):
    requests = []
    offered = uid()

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={'recovery_epoch': offered})

    with MemoryClient('http://memory.test', 'test', recovery_epoch=epoch,
                      transport=httpx.MockTransport(transport)) as client:
        assert client.recovery()['recovery_epoch'] == offered
        client.recovery()
    assert len(requests) == 2
    assert all(request.url.path == '/v1/recovery' for request in requests)
    assert all(request.headers.get('X-Memory-Recovery-Epoch') == epoch for request in requests)


@pytest.mark.parametrize('epoch', ['', 'broken', uid().upper(), True, 1, 'x\r\nInjected: yes'])
def test_invalid_recovery_epoch_is_rejected_before_transport(epoch):
    with pytest.raises(ValueError):
        MemoryClient('http://memory.test', 'test', recovery_epoch=epoch)
