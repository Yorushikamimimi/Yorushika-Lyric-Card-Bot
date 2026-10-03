"""Default entry point for the local Yorushika visual studio."""

from studio import app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
