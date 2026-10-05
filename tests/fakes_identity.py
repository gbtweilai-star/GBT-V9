# tests/fakes_identity.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from botocore.exceptions import ClientError


def client_error(code, status, op):
    """真实 ClientError 形状 → 被测的 _code() 提取逻辑得到真实验证。"""
    return ClientError({"Error": {"Code": code, "Message": code},
                        "ResponseMetadata": {"HTTPStatusCode": status}}, op)


class FakeS3:
    """假 S3 客户端：可注入 owner、错误码、条件写行为。"""
    def __init__(self, *, owner_id="OWNER-1", list_error=None,
                 lock_error=None, version_error=None,
                 ownership_error=None, conditional="reject"):
        self.owner_id, self.list_error = owner_id, list_error
        self.lock_error, self.version_error, self.ownership_error = lock_error, version_error, ownership_error
        self.conditional = conditional          # reject(412) | 501 | allow
        self.store: dict[str, bytes] = {}
        self.calls: list[tuple] = []

    def list_buckets(self):
        self.calls.append(("list_buckets",))
        if self.list_error: raise self.list_error
        return {"Owner": {"ID": self.owner_id}, "Buckets": [{"Name": "b"}]}

    def put_object(self, **kw):
        self.calls.append(("put_object", kw.get("Key"), kw.get("IfNoneMatch")))
        key, body = kw["Key"], kw["Body"]
        if kw.get("IfNoneMatch") == "*" and key in self.store:
            if self.conditional == "reject": raise client_error("PreconditionFailed", 412, "PutObject")
            if self.conditional == "501":    raise client_error("NotImplemented", 501, "PutObject")
        self.store[key] = body
        return {"ETag": "fake-etag"}

    def get_object_lock_configuration(self, Bucket):
        if self.lock_error: raise self.lock_error
        return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}

    def get_bucket_versioning(self, Bucket):
        if self.version_error: raise self.version_error
        return {"Status": "Enabled"}

    def get_bucket_ownership_controls(self, Bucket):
        if self.ownership_error: raise self.ownership_error
        return {"OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}}


class FakeCF:
    """假 Cloudflare /accounts。"""
    def __init__(self, *, accounts=(), success=True, errors=None, raise_exc=None):
        self.accounts, self.success, self.errors, self.raise_exc = accounts, success, errors, raise_exc
        self.calls = 0

    def __call__(self, req, timeout=10):
        self.calls += 1
        if self.raise_exc: raise self.raise_exc
        return {"success": self.success,
                "result": [{"id": a} for a in self.accounts],
                "errors": self.errors or ([{"code": 10000}] if not self.success else [])}
