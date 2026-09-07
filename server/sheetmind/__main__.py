"""Run the SheetMind API with `python -m sheetmind`."""

from dotenv import load_dotenv

load_dotenv()

import uvicorn

from sheetmind.config import settings


def main() -> None:
    uvicorn.run(
        "sheetmind.application:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
