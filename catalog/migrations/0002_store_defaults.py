from decimal import Decimal
from django.db import migrations


def initialize_store(apps, schema_editor):
    StoreSettings = apps.get_model('catalog', 'StoreSettings')
    StoreSettings.objects.using(schema_editor.connection.alias).get_or_create(pk=1, defaults={
        'name': 'CaCaCa', 'tax_rate': Decimal('8.25'), 'currency': 'USD', 'shipping_country': 'US',
    })


class Migration(migrations.Migration):
    dependencies = [('catalog', '0001_initial')]
    operations = [migrations.RunPython(initialize_store, migrations.RunPython.noop)]
