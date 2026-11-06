"""Versioned, operator-owned tenant contract. No credentials or paths in JSON."""
import json
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, ValidationInfo, model_validator
from app.provisioning.errors import ProvisioningError
from app.provisioning.postgres import FOUNDATION, permission_policy

ObjectName = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z_][a-z0-9_]{0,62}$")]
Revision = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_]{0,62}$")]


class TenantContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    contract_version: int = Field(ge=1, le=1, strict=True)
    ready: bool
    required_revision: Revision | None
    required_tables: list[ObjectName] = Field(max_length=128)
    table_privileges: dict[ObjectName, list[Literal["SELECT", "INSERT", "UPDATE", "DELETE"]]] = Field(max_length=128)
    sequence_privileges: dict[ObjectName, list[Literal["USAGE", "SELECT"]]] = Field(max_length=128)
    pending: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def coherent(self, info: ValidationInfo):
        tables = set(self.required_tables)
        if len(tables) != len(self.required_tables):
            raise ValueError("Duplicate required table")
        permission_policy(tables, set(self.sequence_privileges), self.table_privileges, self.sequence_privileges)
        if self.ready:
            foundation_test = bool(info.context and info.context.get("foundation_test"))
            if (not self.required_revision or self.required_revision in {"head", "heads", "base"}
                    or "_provisioning_identity" not in tables):
                raise ValueError("Pinned revision and identity required")
            business = tables - FOUNDATION
            if self.required_revision == "tenant_0001":
                if not foundation_test or business:
                    raise ValueError("Foundation is not a business contract")
            elif not business:
                raise ValueError("Business table required")
            if set(self.table_privileges) != business:
                raise ValueError("Every declared business table needs an explicit policy")
            if any("SELECT" not in operations for operations in self.table_privileges.values()):
                raise ValueError("Required tables must be readable")
        return self

    def canonical(self):
        return json.dumps({"contract_version": self.contract_version, "ready": self.ready,
            "required_revision": self.required_revision, "required_tables": sorted(self.required_tables),
            "table_privileges": {k: sorted(v) for k, v in sorted(self.table_privileges.items())},
            "sequence_privileges": {k: sorted(v) for k, v in sorted(self.sequence_privileges.items())}},
            sort_keys=True, separators=(",", ":"))


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate contract key")
        result[key] = value
    return result


def read_contract(path):
    try:
        with open(path, "rb") as stream:
            raw = stream.read(65537)
    except OSError:
        raise ProvisioningError("tenant_contract_unreadable") from None
    try:
        if len(raw) > 65536:
            raise ValueError("Contract too large")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
        if isinstance(data, dict) and type(data.get("contract_version")) is int and data["contract_version"] != 1:
            raise ProvisioningError("tenant_contract_version_unsupported")
        return TenantContract.model_validate(data)
    except ProvisioningError:
        raise
    except (ValueError, TypeError, ValidationError):
        raise ProvisioningError("tenant_contract_invalid") from None
