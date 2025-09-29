from fastapi.templating import Jinja2Templates
import os
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
