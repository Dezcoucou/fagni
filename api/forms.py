from django import forms
class SingleImageForm(forms.Form):
    image = forms.ImageField(required=False)
