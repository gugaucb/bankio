from django import forms


class LoginForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)


class OTPForm(forms.Form):
    code = forms.CharField(max_length=6, min_length=6)


from django.contrib.auth.forms import PasswordChangeForm


class ChangePasswordForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in ("old_password", "new_password1", "new_password2"):
            self.fields[f].widget.attrs.update({
                "class": "w-full rounded-xl border-gray-200 focus:border-emerald-700 focus:ring-emerald-700",
            })
