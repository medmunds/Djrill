from django import forms


class CreateSenderForm(forms.Form):
    email = forms.EmailField()
