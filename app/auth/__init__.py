"""
Authentication package for the app-first pivot.

Contains registry-DB auth models (User, Device, OtpChallenge,
WebAuthnCredential) and supporting auth logic. All models are registered
on the shared ``Base`` so they are created in the registry database.
"""
