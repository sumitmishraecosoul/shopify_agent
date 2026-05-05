from fastapi import APIRouter

from .auth import login_and_issue_token, signup_user
from .models import AuthLoginRequest, AuthLoginResponse, AuthSignupRequest, AuthSignupResponse


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=AuthLoginResponse)
def login(request: AuthLoginRequest) -> AuthLoginResponse:
    token_payload = login_and_issue_token(request.username, request.password)
    return AuthLoginResponse(**token_payload)


@router.post("/signup", response_model=AuthSignupResponse)
def signup(request: AuthSignupRequest) -> AuthSignupResponse:
    payload = signup_user(request.username, request.password)
    return AuthSignupResponse(**payload)
