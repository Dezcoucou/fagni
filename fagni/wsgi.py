import os

os.environ["DJANGO_SETTINGS_MODULE"] = "fagni.settings"
os.environ["CLOUDINARY_CLOUD_NAME"]  = "dxqeolgih"
os.environ["CLOUDINARY_API_KEY"]     = "812291377152719"
os.environ["CLOUDINARY_API_SECRET"]  = "AJVTqTttX9G3mjpAWXHSEcWpAbY"

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
