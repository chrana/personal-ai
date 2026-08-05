import json
import boto3

_client = boto3.client("secretsmanager", region_name="us-east-1")
_cache = {}


def get_secret(name: str) -> dict:
    if name not in _cache:
        response = _client.get_secret_value(SecretId=name)
        _cache[name] = json.loads(response["SecretString"])
    return _cache[name]


def clear_cache():
    _cache.clear()
