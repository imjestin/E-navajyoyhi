from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model


class EmailBackend(ModelBackend):
    # `request` must be the first positional argument: Django calls every backend as
    # backend.authenticate(request, **credentials) and silently skips any backend
    # whose signature cannot accept it.
    def authenticate(self, request=None, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        try:
            user = UserModel.objects.get(email=username)
        except UserModel.DoesNotExist:
            return None
        else:
            if user.check_password(password):
                return user
        return None
