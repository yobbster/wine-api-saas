from fastapi import FastAPI

app = FastAPI(
    title="Wine API",
    description="An API for managing wines",
    version="0.1.0",
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
