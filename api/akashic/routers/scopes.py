"""The scope catalog, for clients building token UIs or checking what they may do."""
from akashic.routing import ApiRouter
from fastapi import Depends

from akashic.auth.dependencies import Principal, get_principal
from akashic.auth.scopes import ROLE_SCOPES, SCOPES, forbidden_with_source_limit, is_read_only
from akashic.schemas.user import ScopeCatalog, ScopeInfo

router = ApiRouter(tags=["auth"])


@router.get(
    "/scopes",
    response_model=ScopeCatalog,
    summary="List scopes and role bundles",
    description=(
        "Every scope the API defines, and the scopes each role grants. Clients use it to build token "
        "pickers. Requires authentication but no particular scope. Errors: 401 for missing or invalid "
        "credentials."
    ),
)
async def list_scopes(_: Principal = Depends(get_principal)) -> ScopeCatalog:
    return ScopeCatalog(
        scopes=[ScopeInfo(
                name=n, description=d,
                allowed_with_source_limit=not forbidden_with_source_limit([n]),
                read_only=is_read_only(n),
            ) for n, d in SCOPES.items()],
        roles={role: sorted(scopes) for role, scopes in ROLE_SCOPES.items()},
    )
