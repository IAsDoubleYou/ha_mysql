"""The ha_mysql component."""
DOMAIN = "ha_mysql"
PLATFORMS = ["sensor"]


def setup(hass, config):
    """Set up the ha_mysql component."""
    # Retrieve the component configuration
    config_key = config[DOMAIN]

    # Store the domain configuration at an easy to retrieve address
    hass.data[DOMAIN] = {'platform': config_key}

    # Store the entity configuration at an easy to retrieve address
    for sensor in config['sensor']:
        platform = sensor['platform']
        name = sensor['name']
        if (platform == DOMAIN):
            key = DOMAIN + "." + name
            hass.data[DOMAIN][key] = sensor

    return True
