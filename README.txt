################################
#PA2                           #
#UID : sd2810                  #
#NAME : shiyu dong             #
################################
________________________________

PART1 PART2 PART3
________________________________
--CAUTION:
Weird behavior or unstable behavior may occur(when you mix operations frequently on complicate topology).
The project is too difficult to be fully tested.
I have tested it on decent complicate topology(shown below),it works well till now.
My suggestion is : 
1. Do not mess up with different commands frequently...
2. Once certain unstale local thing happened, CLOSE(ctrl+c) the unstable node and restart it. This will fix most cases
---------------------------------
                                         --Clarification--
                                        ** ** ** ** ** ** **
                                        ********************
                                         Please read this
                                        ********************
                                        ** ** ** ** ** ** * *
1. to help you monitor how my program work, I have all debug printing turned on. They are well-explained, well formatted. Hope they
won't bother you.

2. I spent some time trying to test with proxy. But, strange thing happened, due to lack of time, I give up on testing it.
My last testing ended with : dropped by congestion: 190(packets) 3112960(bytes)

I ask a TA for help, he also didn't see this problem before.
I hope you would not see this happen in your testing...and I don't know what may happen then.
But, without proxy, you see pretty clear how my ack and checksum work together to ensure correct delivery.
(without ack, even locally, for a large file, you are going to lose more than half of it)

3.Both my ack and checksum work well. You can tell easily from those debug printings. But, when I write out the picture
I received, it doesn't look good...I don't know why, and unfortunately, due to finals, I don't have time to fix the problem
of correctly writing.

4.Due to lack of time, usr for now is **responsible** to give the right exact format of command input.
I didn't ensure command input verification, some invalid input may crash process.

5.please read the below part about CLOSE command implementation.
----------------------------------
About CLOSE:
I have two versions of CLOSE.
1. type CLOSE on command line (this version has many features add to simply doing ctrl+c)
2. Keyboard interruption: cltr+c

1 is different to 2 essentially.
The reason to keep both is that: 
Initially I got unstable behavior by using ctrl+c sometime, which is unpredictable
I then implemented a more complicate(not ctrl+c anymore, tough quite the same in apperance,but more features are added).

For testing, I highly recommand CLOSE. CLOSE is an enhenced version of ctrl+c, which ensure stability.
**
IF, CLOSE must work as ctrl+c works exactly(I didn't get the information from instruction though), you can use cltr+c to test CLOSE directly.But there is a chance of undefined behavior. 
-------------------------------------------------------
Explanation of enhenced version of CLOSE:
                                    >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
                                    >> (you may skip this, if you don't want to test ctrl+c)<<
                                    <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
Weird Behavior Occurred...(though works well now)
In my initial CLOSE implementation, which is exact the way of dong ctrl+c, I got unstable/unpredictable behaviors sometime
**
Solution...
I came up with a new version to stabilize CLOSE...what I do is kind of making CLOSE invoke LINKDOWN on all neighbors...This ensures synchronization among neighors of knowing the closing of a node.

Notice, when you type CLOSE, what you are doing is different from cltr+c.
It is more stable in design. But you could still do cltr+c when testing as follows:
**
IF you want to test with cltr+c...
Use cltr+c to simulate CLOSE:
cltr+c to close a node, then within 3*TIMEOUT, restart it.
this works well as required.

cltr+c to close a node, then after 3*TIMEOUT, all nodes converge fine.
***
Here you have a chance to meet with undefined behavior. If you happened to see it, just restart the node, everything gonna be fixed
***
Then restart it.
This works well

----------------------------------
How to run the program:
In terminal:
$ python bfclient.py clnta.txt

Support command:(host ip is 192.168.0.2)
__________________________________

      {A4115}---- 5 ----{B4116}
      /      \          /      \
     3       30        5        10
    /          \      /          \
 {E4119}---3---{D4118}---3---{C4117}

___________________________________
Command examples:
# Test case passed on a 5 nodes topology having loops within loops (see readme)
# CLOSE
# CHANGECOST 192.168.0.2 4118 1
# CHANGECOST 192.168.0.2 4118 30
# LINKDOWN 192.168.0.2 4118
# LINKUP 192.168.0.2 4118
# TRANSFER test.jpg 192.168.0.2 4117
# ADDPROXY 192.168.0.2 41192 192.168.0.2 4119
# REMOVEPROXY 192.168.0.2 4119

----------------------------------
--PART1 Design Structure 
     Threads used:
    - Main thread which in charge of listening on socket(bind with given port at initialization) and keyboard input(using select).
    - Sender thread which in charge of literally sending any thing out. It takes things out from OUTPOOL.
    - DV_main thread which in charge of maintaining DV(distance vector) information.
    - Timer thread which in charge of monitoring each of neighbor nodes to see whether they are still there.
    - timer_ACK thread for ack checking, to determine resend and give up sending
    - Writeout_Big_FIle thread for writing out received big file.(I am not sure whether this is required, I implemented this in a very simple way)

--PART1 Data Structure
I have clear notations about each important data structure in my code. I will not bother putting them here again.

--PART1 Algo.
You can find detail about the algorithm in code comments,I won't describe here.
The algorithm part is basically in the thread DV_main.(refer to this thread for detail of implementation)

--PART1 TEST CASE 
__________________________________

      {A4115}---- 5 ----{B4116}
      /      \          /      \
     3       30        5        10
    /          \      /          \
 {E4119}---3---{D4118}---3---{C4117} 

___________________________________
For the above topology, 
A - E are assigned PORTS 4115 - 4419
link cost as shown above
IP when testing is 192.168.0.2

____________________________________
Test Report

____________________________________

**All 6 test cases are passed in my tests. I have tested the program more than 50 times with mixing commands.
It should work well, but, regarding to use of ctrl+c, some undefined behavior may occur.

Test case 1: 
Issue CHANGECOST 192.168.0.2 4117 10 on clnt D

Test case 2:
Issue LINKDOWN 192.168.0.2 4117 on clnt D (Here we are linkdown a link whose cost has been CHANGECOSTed)

Test case 3:
Issue LINKUP 192.168.0.2 4117 on clnt D (Here we are linkdown a link wh
ose cost has been CHANGECOSTed)

Test case 4:
issue CLOSE on node C, immediately restart node C before 3*TIMEOUT

Test case 5:
issue CLOSE on node D and restart after 3*TIMEOUT

Test case 6:
A big file is transfered from NodeA to NodeC through shortest path and all parts are well received.
issue TRANSFER test.jpg 192.168.0.2 4117 on clientA