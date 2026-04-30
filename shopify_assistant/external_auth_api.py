from fastapi import APIRouter

from .auth import login_and_issue_token
from .models import AuthLoginRequest, AuthLoginResponse


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=AuthLoginResponse)
def login(request: AuthLoginRequest) -> AuthLoginResponse:
    token_payload = login_and_issue_token(request.username, request.password)
    return AuthLoginResponse(**token_payload)
