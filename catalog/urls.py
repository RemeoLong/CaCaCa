from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('shop/', views.shop, name='shop'),
    path('tackle/', views.tackle, name='tackle'),
    path('tackle/<slug:slug>/', views.tackle_product, name='tackle_product'),
    path('salmon-rods/', views.shop, {'salmon': True}, name='salmon'),
    path('gallery/', views.shop, {'gallery': True}, name='gallery'),
    path('rods/<slug:slug>/', views.product, name='product'),
    path('custom-rods/', views.custom, name='custom'),
    path('custom-rods/received/', views.page, {'page': 'custom_success'}, name='custom_success'),
    path('about/', views.page, {'page': 'about'}, name='about'),
    path('faq/', views.page, {'page': 'faq'}, name='faq'),
    path('contact/', views.contact, name='contact'),
    path('contact/received/', views.page, {'page': 'inquiry_success'}, name='inquiry_success'),
    path('returns/', views.page, {'page': 'returns'}, name='returns'),
    path('health/', views.health, name='health'),
    path('robots.txt', views.robots, name='robots'),
]
