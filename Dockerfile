FROM python:3.7.7-slim-buster
LABEL maintainer="Eliezio Oliveira <eliezio@pm.me>"

# Install deps all in one step
RUN set -eux; apt-get update -y; \
    apt-get install -y \
            apt-transport-https \
            ca-certificates \
            curl; \
    # Clean up the lists work
    rm -rf /var/lib/apt/lists/*; \
    pip3 install -U pip; \
    pip3 install pipenv

ENV HOME=/app
RUN adduser --disabled-password --home $HOME --gecos Pulumi pulumi
USER pulumi
WORKDIR $HOME

# Install the Pulumi SDK, including the CLI and language runtimes.
ARG pulumi_version=2.1.0
RUN curl --proto '=https' --tlsv1.2 -fsSL https://get.pulumi.com/ | sh -s -- --version $pulumi_version

ENV PATH=$HOME/.pulumi/bin:/usr/local/bin:/usr/bin:/bin

# Install Pulumi Plugins
ARG pulumi_plugin_aws_version=2.3.0
ARG pulumi_plugin_mysql_version=2.1.0
ARG pulumi_plugin_okta_version=2.1.0

RUN set -eux; \
    pulumi plugin install resource aws $pulumi_plugin_aws_version; \
    pulumi plugin install resource mysql $pulumi_plugin_mysql_version; \
    pulumi plugin install resource okta $pulumi_plugin_okta_version

# Install required Python packages
COPY Pipfile* ./
RUN pipenv install --system

# Copy application
COPY entrypoint.sh *.py Pulumi.yaml ./
COPY main/ $HOME/main/

# Define volume with user configuration
VOLUME $HOME/config

ENTRYPOINT [ "./entrypoint.sh" ]
CMD [ "preview" ]
