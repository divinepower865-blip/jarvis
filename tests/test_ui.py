from conftest import TOKEN


def test_chat_shell_public_but_data_private(client):
    response = client.get('/', headers={'Authorization':'invalid'})
    assert response.status_code == 200
    assert 'Message JARVIS' in response.text
    assert TOKEN not in response.text
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    assert client.get('/assets/app.js', headers={'Authorization':'invalid'}).status_code == 200
    assert client.get('/v1/conversations', headers={'Authorization':'invalid'}).status_code == 401


def test_static_paths_do_not_expose_configuration(client):
    for path in ['/assets/../.env','/assets/%2e%2e/config.py','/assets/.env']:
        response=client.get(path, headers={'Authorization':'invalid'})
        assert response.status_code in (401,404)
