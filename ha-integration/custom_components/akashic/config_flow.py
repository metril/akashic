import httpx
import voluptuous as vol

from homeassistant import config_entries

from .const import DOMAIN, CONF_API_URL, CONF_API_KEY


class AkashicConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{user_input[CONF_API_URL]}/api/sources",
                        headers={"Authorization": f"Bearer {user_input[CONF_API_KEY]}"},
                        timeout=10,
                    )
                    resp.raise_for_status()
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="Akashic", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_URL, default="http://localhost:8000"): str,
                vol.Required(CONF_API_KEY): str,
            }),
            errors=errors,
        )
