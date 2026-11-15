"""The scope catalog, for clients building token UIs or checking what they may do."""
from fastapi import APIRouter, Depends

from akashic.auth.dependencies import Principal, get_principal
from akashic.auth.scopes import ROLE_SCOPES, SCOPES
from akashic.schemas.user import ScopeCatalog, ScopeInfo

router = APIRouter(tags=["auth"])


@router.get(
    "/scopes",
    response_model=ScopeCatalog,
    summary="List scopes and role bundles",
    description=(
        "Every scope the API defines, and the scopes each role grants. "
        "Requires authentication but no particular scope."
    ),
)
async def list_scopes(_: Principal = Depends(get_principal)) -> ScopeCatalog:
    return ScopeCatalog(
        scopes=[ScopeInfo(name=n, description=d) for n, d in SCOPES.items()],
        roles={role: sorted(scopes) for role, scopes in ROLE_SCOPES.items()},
    )
