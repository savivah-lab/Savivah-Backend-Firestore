import os
import resend


def _get_api_key() -> str:
    value = os.getenv("RESEND_API_KEY")

    if not value:
        raise RuntimeError("RESEND_API_KEY is not configured")

    return value


def _get_from_email() -> str:
    return os.getenv(
        "RESEND_FROM_EMAIL",
        "onboarding@resend.dev",
    )


async def send_verification_email(
    to_email: str,
    code: str,
) -> None:
    resend.api_key = _get_api_key()

    params = {
        "from": _get_from_email(),
        "to": [to_email],
        "subject": "Verify your Savivah email",
        "html": f"""
        <div style="
            font-family: Arial, sans-serif;
            max-width: 560px;
            margin: 0 auto;
            padding: 24px;
        ">
            <h2 style="color:#161513;">
                Verify your Savivah email
            </h2>

            <p>
                Thank you for creating your Savivah account.
            </p>

            <p>
                Your 6-digit verification code is:
            </p>

            <div style="
                font-size:32px;
                font-weight:700;
                letter-spacing:8px;
                padding:18px;
                margin:20px 0;
                background:#FBF1DA;
                border-radius:8px;
                text-align:center;
                color:#9C740F;
            ">
                {code}
            </div>

            <p>
                This code expires in 10 minutes.
            </p>

            <p>
                If you did not create a Savivah account,
                you can safely ignore this email.
            </p>

            <p>
                — Savivah
            </p>
        </div>
        """,
    }

    result = await resend.Emails.send_async(params)

    if getattr(result, "error", None):
        raise RuntimeError(str(result.error))
