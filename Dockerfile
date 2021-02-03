FROM dependabot/dependabot-core:0.130.3

# Native dependency needed for neo4j communication from Ruby
RUN wget https://github.com/neo4j-drivers/seabolt/releases/download/v1.7.4/seabolt-1.7.4-Linux-ubuntu-$(lsb_release -rs).deb && \
    dpkg -i seabolt-1.7.4-Linux-ubuntu-$(lsb_release -rs).deb && \
    rm seabolt-1.7.4-Linux-ubuntu-$(lsb_release -rs).deb
