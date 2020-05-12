FROM python:3.7.7-slim-buster as stage0

RUN set -uex; \
    apt-get update -yqq; \
    apt-get upgrade -yq; \
    rm -rfv \
        /var/lib/apt/lists/* \
        /var/cache/debconf/* \
        /var/log/dpkg.log \
        /var/log/lastlog

FROM scratch
LABEL maintainer="Eliezio Oliveira <eliezio@pm.me>"
COPY --from=stage0 / /

ENV HOME=/app
RUN set -eux; \
    adduser --disabled-password --home $HOME --gecos Pulumi pulumi; \
    rm -vf /var/log/lastlog

# Install the Pulumi SDK, including the CLI and language runtimes.
ARG pulumi_version=2.1.1
ARG pulumi_plugin_aws_version=2.4.0
ARG pulumi_plugin_mysql_version=2.1.1
ARG pulumi_plugin_okta_version=2.1.1

# Install Pulumi & Plugins in one go
# Optmizations that saves 175MB:
#   1. Removed non-used language-oriented runtimes
#   2. Stripped binaries
# Modified folder: $HOME/.pulumi
RUN set -eux; \
    apt-get update -yqq; \
    apt-get install binutils curl -yq; \
    curl --proto '=https' --tlsv1.2 -fsSL https://get.pulumi.com/ | sh -s -- --version $pulumi_version; \
    for lang in dotnet go nodejs; do rm -v $HOME/.pulumi/bin/pulumi-language-$lang; done; \
    strip --strip-unneeded --preserve-dates \
        $HOME/.pulumi/bin/pulumi \
        $HOME/.pulumi/bin/pulumi-language-python; \
    chown -R pulumi $HOME/.pulumi; \
    for p in aws mysql okta; do \
        eval version=\$pulumi_plugin_${p}_version; \
        su pulumi -c "$HOME/.pulumi/bin/pulumi plugin install resource $p $version"; \
        strip --strip-unneeded --preserve-dates $HOME/.pulumi/plugins/resource-${p}-v$version/pulumi-resource-${p}; \
    done; \
    chown -R pulumi:pulumi $HOME; \
    chmod -R go=u-w $HOME; \
    apt-get autoremove -yq binutils curl; \
    rm -rfv \
        /var/lib/apt/lists/* \
        /var/cache/debconf/* \
        /var/log/dpkg.log

USER pulumi
WORKDIR $HOME
ENV PATH=$HOME/.pulumi/bin:/usr/local/bin:/usr/bin:/bin

# Install required Python packages
# Modified folder: $HOME/.local
COPY --chown=pulumi:pulumi Pipfile* ./
RUN set -eux; \
    PATH=$HOME/.local/bin:$PATH; \
    pip3 install --disable-pip-version-check --no-cache-dir pipenv; \
    pipenv install --system; \
    rm -rf $HOME/.cache $HOME/.local/virtualenvs; \
    pip3 uninstall --disable-pip-version-check --yes pipenv virtualenv virtualenv-clone; \
    find $HOME/.local -type d -name __pycache__ \
        -exec rm -rf {} \; -prune; \
    rm -rv $HOME/.local/lib/python3.7/site-packages/mysql-vendor; \
    find $HOME/.local/lib/python3.7 -name \*.so \
        -exec strip --strip-unneeded --preserve-dates {} \;

# Copy application
COPY --chown=pulumi:pulumi entrypoint.sh *.py Pulumi.yaml ./
COPY --chown=pulumi:pulumi main/ $HOME/main/

ENTRYPOINT [ "./entrypoint.sh" ]
CMD [ "preview" ]
