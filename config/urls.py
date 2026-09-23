from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from catalog import views as catalog_views

urlpatterns = [path('admin/', admin.site.urls), path('sitemap.xml', catalog_views.sitemap, name='sitemap'),
               path('', include('commerce.urls')), path('', include('catalog.urls'))]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
