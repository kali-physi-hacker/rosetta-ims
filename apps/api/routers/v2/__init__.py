from fastapi import FastAPI


def include_routers(target: FastAPI, *, include_in_schema: bool = True) -> None:
    """v2 starts empty; register future v2 routers here."""
