from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, Request


@dataclass
class Principal:
    student_id: str
    courses: list[str]
    admin: bool = False
    development: bool = False

    def require_course(self, course_id: str):
        if not (self.development or self.admin or course_id in self.courses):
            raise HTTPException(403, "Нет доступа к курсу")


def principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_student_id: str = Header(default="demo-student"),
) -> Principal:
    config = request.app.state.settings
    if not config.auth_enabled:
        if not 1 <= len(x_student_id) <= 128:
            raise HTTPException(422, "Некорректный X-Student-ID")
        return Principal(x_student_id, [], development=True)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Требуется Bearer token")
    try:
        claims = jwt.decode(
            authorization[7:],
            config.jwt_secret,
            algorithms=["HS256"],
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
            options={"require": ["sub", "exp", "iss", "aud"]},
        )
        courses = claims.get("courses", [])
        if not isinstance(courses, list) or not all(isinstance(c, str) for c in courses):
            raise ValueError("Invalid courses claim")
        if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 128:
            raise ValueError("Invalid subject")
        return Principal(claims["sub"], courses, admin=claims.get("role") == "admin")
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(401, "Недействительный токен") from None


def admin(user: Principal = Depends(principal)):
    if not (user.development or user.admin):
        raise HTTPException(403, "Требуются права администратора")
    return user
