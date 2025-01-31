import logging

from copy import deepcopy

from django.apps import apps
from django.template.response import TemplateResponse
from django.conf import settings
from django.contrib import admin
from django.core.exceptions import ImproperlyConfigured
from django.urls import Resolver404, resolve
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


class ModelAdminReorder(MiddlewareMixin):
    settings_config = "ADMIN_REORDER_MODEL_LIST"
    settings_valid_url_names = "ADMIN_REORDER_VALID_URL_NAMES"
    settings_append_unrepresented_models = "ADMIN_APPEND_UNREPRESENTED_MODELS"

    def init_valid_urls(self, request):
        self.valid_url_names = getattr(
            settings, self.settings_valid_url_names, ["index", "app_list"]
        )
        logger.info(
            f"End of init_valid_urls:\n\nself.request: "
            f"\n\nself.valid_url_names = {self.valid_url_names}"
        )

    def init_config(self, request, response):
        """
        Initializes configuration
        """
        self.request = request
        self.config = getattr(settings, self.settings_config, None)
        self.append_unrepresented_models = getattr(
            settings, self.settings_append_unrepresented_models, False
        )

        if not self.config:
            # ADMIN_REORDER settings is not defined.
            raise ImproperlyConfigured(f"{self.settings_config} config is not defined.")

        if not isinstance(self.config, (tuple, list)):
            raise ImproperlyConfigured(
                f"{self.settings_config} config parameter must be tuple or list. "
                f"Got {self.config}"
            )

        if not isinstance(self.valid_url_names, (tuple, list)):
            raise ImproperlyConfigured(
                f"{self.settings_valid_url_names} parameter must be tuple or list. "
                f"Got {self.valid_url_names}"
            )

        self.project_apps_list = self.get_project_apps_list(response)
        self.project_models_list = self.get_project_models_list()

        logger.info(
            f"End of init_config:\n\nself.request: "
            f"{self.request}\n\nself.config = {self.config}"
            f"\n\nself.response_context_key = {self.response_context_key}"
            f"\n\nself.project_apps_list = {self.project_apps_list}"
            f"\n\nself.project_models_list = {self.project_models_list}"
        )

    def init_response_context_key(self, response):
        """
        Get the correct context_key for the response, and if
        present, set `self.response_context_key`
        """
        logger.info(f"response.context_data = {response.context_data}")

        if "app_list" in response.context_data:
            response_context_key = "app_list"
            logger.info(f"key 'app_list' in response.context_data")
        elif "available_apps" in response.context_data:
            response_context_key = "available_apps"
            logger.info(f"key 'available_apps' in response.context_data")
        else:
            # there is no app_list! nothing to reorder
            logger.info(f"No app list available in response.context_data")
            response_context_key = None

        self.response_context_key = response_context_key
        logger.info(
            f"response_context_key = {response_context_key}, self.response_context_key = {self.response_context_key}"
        )
        return

    def get_project_apps_list(self, response):
        """
        Returns a listing of all installed apps in the project
        pulled from response context
        """
        logger.info(f"self.response_context_key = {self.response_context_key}")
        return response.context_data[self.response_context_key]

    def get_project_models_list(self):
        """
        Returns a flat listing of all models within installed apps in the project
        """
        project_models_list = []
        for app in self.project_apps_list:
            for model in app["models"]:
                model["model_name"] = self.get_formatted_model_name(
                    app["app_label"], model["object_name"]
                )
                project_models_list.append(model)
        return project_models_list

    def get_formatted_model_name(self, app_name, model_name):
        """
        Formats the model name if needed
        """
        if "." not in model_name:
            model_name = f"{app_name}.{model_name}"
        return model_name

    def get_reordered_apps_list(self):
        """
        Returns the final ordered list of apps used by Admin in
        the middleware response

        ToDo:   Before we return the final reordered list for the response, if 
                self.append_unrepresented_models is True, then we need to identify
                which models are not yet represented in the app_config, and create
                and append one more app_cofig to represent these.
        """

        reordered_apps_list = [
            self.process_app_config(app_config) for app_config in self.config
        ]
        logger.info(f"reordered_apps_list = {reordered_apps_list}")
        return reordered_apps_list

    def process_app_config(self, app_config):
        """
        Process each app_config entry item in the config
        Entries must be of type `dict` or `str`
        """
        if not isinstance(app_config, (dict, str)):
            raise TypeError(
                f"{self.settings_config} list item must be "
                f"dict or string. Got {repr(app_config)}"
            )

        if isinstance(app_config, str):
            # Keep original label and models
            return self.get_valid_app_from_str(app_label=app_config)
        else:
            return self.get_valid_app_from_dict(app_config=app_config)

    def get_valid_app_from_str(self, app_label):
        """
        Given an app_config item that is an app_label, check that the app
        actually exists in the project. Returns the app if it is valid.
        """
        for app in self.project_apps_list:
            if app["app_label"] == app_label:
                return app

    def get_valid_app_from_dict(self, app_config):
        """
        Given an app_config item that is a dict, process and return the
        finalized app config.
        """
        if "app" not in app_config:
            raise NameError(
                f"{self.settings_config} list item must define "
                f'a "app" name. Got {repr(app_config)}'
            )

        # Get the app based on the app's label in the app_config
        app = self.get_valid_app_from_str(app_config["app"])

        if app:
            app = deepcopy(app)

            # Rename the app if a label was provided in the app_config
            if "label" in app_config and isinstance(app_config["label"], (str,)):
                app["name"] = app_config["label"]

            # Get the dict, list, or tuple of models from the app_config and process them
            if "models" in app_config:
                models_config = app_config.get("models")
                models = self.process_models_config(models_config)
                if models:
                    app["models"] = models
                else:
                    return None
            return app

    def process_models_config(self, models_config):
        """
        Given a models_config consisting of dict, list, or tuple,
        processes and return a validated, ordered list of models
        """
        if not isinstance(models_config, (dict, list, tuple)):
            raise TypeError(
                f'"models" config for {self.settings_config} list '
                "item must be dict or list/tuple. "
                f"Got {repr(models_config)}"
            )

        initial_models_list = []
        for model_config in models_config:
            model = None
            if isinstance(model_config, dict):
                model = self.process_model_config(model_config=model_config)
            else:
                # str model_label e.g.: app.Model
                if not ".*" in model_config:
                    model = self.get_valid_model_from_str(model_name=model_config)
                else:
                    # Deal with wildcards in a model_config entry
                    models = self.process_model_config_wildcard(
                        model_wildcard=model_config
                    )
                    for model in models:
                        if model:
                            initial_models_list.append(model)

            if model:
                initial_models_list.append(model)

        logger.info(f"initial_models_list = {initial_models_list}")
        ordered_models_list = self.get_deduplicated_models_list(initial_models_list)
        logger.info(f"ordered_models_list = {ordered_models_list}")

        return ordered_models_list

    def get_deduplicated_models_list(self, models_list):
        """
        Convert each model_dict into a model_tuple (first converting the internal
        perms dict to a tuple). Then append the tuple to a models_list. Next 
        convert the list of tuples to a set of tuples, removing the duplicates
        Finally, convert everything back.
        """
        perms = None

        ordered_models_tuple_list = []
        for model_dict in models_list:

            # perms is a dict inside the overall model_dict, so convert it first
            perms = model_dict.get("perms")
            if isinstance(perms, dict):
                perms = tuple(perms.items())
                model_dict["perms"] = perms

            # Convert the models_dict to a tuple and append to the list
            model_tuple = tuple(model_dict.items())
            ordered_models_tuple_list.append(model_tuple)

        # Deduplicate by converting the whole list to set
        models_set = set(ordered_models_tuple_list)

        # Convert back to dictionaries
        models_dict_list = []
        for deduped_model in models_set:
            deduped_model_dict = dict(deduped_model)

            # Again, process perms
            deduped_model_dict["perms"] = dict(deduped_model_dict["perms"])
            models_dict_list.append(dict(deduped_model_dict))

        return models_dict_list

    def get_valid_model_from_str(self, model_name):
        """
        Search for the model in the list of all models, returning it if found
        """
        for model in self.project_models_list:
            if model["model_name"] == model_name:
                return model

    def process_model_config(self, model_config):
        """
        Process model_config defined as { model: 'model', 'label': 'label' }
        """
        for key in (
            "model",
            "label",
        ):
            if key not in model_config:
                # model_config is invalid, return None
                return

        model = self.get_valid_model_from_str(model_config["model"])
        if model:
            model["name"] = model_config["label"]
            return model

    

    def process_model_config_wildcard(self, model_wildcard):
        """
        If we have a wildcard in a model_config (e.g.: "auth.*"),
        identify and process all models belonging to that app
        """

        # Get the app name from the string
        app_name = model_wildcard.split(".*")[0]

        # Get a list of model names for the app
        app_models = apps.get_app_config(app_name).get_models()

        valid_model_list = []
        for model in app_models:
            valid_model = self.get_valid_model_from_str(model._meta.label)
            if valid_model:
                valid_model_list.append(valid_model)

        def get_model_list_key(model):
            """Used to sort the list alphabetically by `model_name`"""
            return model["model_name"]

        valid_model_list.sort(key=get_model_list_key)

        return valid_model_list

    def validate_admin_urls(self, request):
        """
        Checks that we are admin and that the current url_name
        matches one in the provided list of url names.
        Defaults to `["index", "app_list"]`
        """
        try:
            url = resolve(request.path_info)
        except Resolver404:
            return False

        if not url.app_name == "admin" and url.url_name not in self.valid_url_names:
            # current view is not a django admin index
            # or app_list view, bail out!
            return False

        return True

    def process_template_response(self, request, response):
        """
        Called in the middleware to return the TemplateResponse object
        https://docs.djangoproject.com/en/4.1/topics/http/middleware/#process-template-response
        """

        self.init_valid_urls(request)
        if not self.validate_admin_urls(request):
            # Current view is not a valid django admin view
            # bail out!
            return response

        # Get the context_key for response, returning response now if not present
        self.init_response_context_key(response)
        if self.response_context_key is None:
            return response

        self.init_config(request, response)

        # Replace the original app list in the context with our reordered app list
        response.context_data[
            self.response_context_key
        ] = self.get_reordered_apps_list()
        return response
