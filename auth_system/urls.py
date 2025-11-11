from django.urls import path
from .views import test_page_view, search_by_name

urlpatterns = [
    path('', test_page_view, name='test_page'),
    path('search_by_name/', search_by_name, name='search_by_name'),
]