# Host-Tests fuer pst_core (ohne Hardware):  make -C test
CXX ?= g++
CXXFLAGS ?= -std=c++11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined

all: run host_session

# Sitzung wie mit PyST (#AI, A-Zeilen), Ausgabe auf stdout; PyST/tests/test_firmware_analog.py liest sie
host_session: host_session.cpp ../pst_core.cpp ../pst_core.h
	$(CXX) $(CXXFLAGS) -o $@ host_session.cpp ../pst_core.cpp

test_core: test_core.cpp ../pst_core.cpp ../pst_core.h
	$(CXX) $(CXXFLAGS) -o $@ test_core.cpp ../pst_core.cpp

run: test_core
	./test_core

clean:
	rm -f test_core host_session

.PHONY: all run clean
