"""Public login assets must not process attacker-controlled range explosions."""
from fastapi.testclient import TestClient

from agent.service import app


def test_public_asset_ignores_excessive_ranges():
    # Avoid startup hooks: this test does not need a PostgreSQL connection.
    client = TestClient(app)
    normal = client.get('/login.css')
    assert normal.status_code == 200
    # This fits the proxy's header limit. Older Starlette processes all ranges
    # and returns 206; the patched parser bounds the work and ignores the range.
    header = 'bytes=' + ','.join(['0-0'] * 1000)
    response = client.get('/login.css', headers={'Range': header})
    assert response.status_code == 200
    assert response.content == normal.content
    assert 'content-range' not in response.headers
