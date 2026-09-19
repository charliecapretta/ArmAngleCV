"""Elastic Beanstalk Python 3.12 WSGI entry point."""
from hosted.app import create_app

application = create_app()
