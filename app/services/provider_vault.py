"""Provider credentials are encrypted and bound to a specific owner/provider."""

import json

from cryptography.fernet import Fernet


class ProviderVault:
    def __init__(self, key):
        self.cipher = Fernet(key.encode())

    def seal(self, user_id, provider, secret):
        return self.cipher.encrypt(
            json.dumps({"owner": user_id, "provider": provider, "secret": secret}).encode()
        ).decode()

    def open(self, user_id, provider, ciphertext):
        payload = json.loads(self.cipher.decrypt(ciphertext.encode()))
        if payload.get("owner") != user_id or payload.get("provider") != provider:
            raise ValueError("Credential owner mismatch")
        return payload["secret"]
