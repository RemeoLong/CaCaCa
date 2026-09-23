from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('catalog', '0005_product_build_cost')]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='price',
            field=models.DecimalField(
                blank=True,
                null=True,
                decimal_places=2,
                max_digits=10,
                validators=[MinValueValidator(Decimal('0.01'))],
                help_text='Required before this rod can be published. Leave blank while preparing a private draft.',
            ),
        ),
        migrations.AddConstraint(
            model_name='product',
            constraint=models.CheckConstraint(
                condition=models.Q(is_published=False) |
                          (models.Q(price__isnull=False) & models.Q(price__gt=0)),
                name='published_product_has_price',
            ),
        ),
    ]
