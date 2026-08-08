import django_filters

from .models import *

class TableFilter(django_filters.FilterSet):
    class Meta:
        models = Staff
        fields = '__all__'
        # exclude = ['aadhar_num','password','profile_pic', 'address']