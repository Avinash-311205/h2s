"""Entrypoint for the Module 6 API. Run with: python main.py"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8006,
        reload=True,
    )