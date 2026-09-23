"""One-time owner account claim for the temporary Render review site."""

from hashlib import sha256
from secrets import compare_digest

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from catalog.models import StoreSettings


class OwnerSetupForm(UserCreationForm):
    email = forms.EmailField(label='Email address')
    setup_code = forms.CharField(label='One-time setup code', widget=forms.PasswordInput)

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ('username', 'email')

    def clean_setup_code(self):
        code = self.cleaned_data['setup_code']
        expected = settings.OWNER_SETUP_CODE_HASH
        if not expected or not compare_digest(sha256(code.encode()).hexdigest(), expected):
            raise forms.ValidationError('That setup code is not correct.')
        return code


@sensitive_post_parameters('setup_code', 'password1', 'password2')
@never_cache
@require_http_methods(['GET', 'POST'])
def owner_setup(request):
    user_model = get_user_model()
    if not settings.REVIEW_MODE or not settings.OWNER_SETUP_CODE_HASH or user_model.objects.filter(is_staff=True).exists():
        raise Http404

    form = OwnerSetupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            # Serialize first-account claims against the singleton settings row.
            StoreSettings.objects.get_or_create(pk=1)
            StoreSettings.objects.select_for_update().get(pk=1)
            if user_model.objects.filter(is_staff=True).exists():
                raise Http404
            user = form.save(commit=False)
            user.is_staff = True
            user.is_superuser = True
            user.save()
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return redirect('owner_dashboard')
    return render(request, 'owner/setup.html', {'form': form})
