# Host-Tests fuer pst_core (ohne Hardware):  make -C test
CXX ?= g++
CXXFLAGS ?= -std=c++11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined

all: run

test_core: test_core.cpp ../pst_core.cpp ../pst_core.h
	$(CXX) $(CXXFLAGS) -o $@ test_core.cpp ../pst_core.cpp

run: test_core
	./test_core

clean:
	rm -f test_core

.PHONY: all run clean
